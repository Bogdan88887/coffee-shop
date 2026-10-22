const express = require("express");
const fs = require("fs");
const path = require("path");

const app = express();
const PORT = 3000;
app.set("trust proxy", "loopback");

const COMMENTS_FILE = path.join(__dirname, "comments.json");
const MAX_COMMENTS_FILE_SIZE = 3 * 1024 * 1024;
const MAX_ACTIVE_COMMENT_REQUESTS = 30;
const IP_BLOCK_DURATION_MS = 15 * 60 * 1000;
const MAX_COMMENT_LENGTH = 45;
const MAX_NAME_LENGTH = 10;
const COMMENT_AVATARS = Array.from(
    { length: 10 },
    (_, index) => `img/avatars/avatar-${String(index + 1).padStart(2, "0")}.svg`
);

let activeCommentRequests = 0;
let commentsWriteQueue = Promise.resolve();
const blockedIps = new Map();

app.use(express.json({ limit: "8kb" }));
app.use("/comments.json", (req, res) => res.sendStatus(404));
app.use(express.static(__dirname));

if (!fs.existsSync(COMMENTS_FILE)) {
    fs.writeFileSync(COMMENTS_FILE, "[]");
}

function getClientIp(req) {
    return req.ip || req.socket.remoteAddress || "unknown";
}

function isIpBlocked(ip) {
    const blockedUntil = blockedIps.get(ip);
    if (!blockedUntil) return false;
    if (blockedUntil <= Date.now()) {
        blockedIps.delete(ip);
        return false;
    }
    return true;
}

async function readComments() {
    const contents = await fs.promises.readFile(COMMENTS_FILE, "utf8");
    const comments = JSON.parse(contents);
    return Array.isArray(comments) ? comments : [];
}

function serializeComments(comments) {
    return JSON.stringify(comments, null, 2);
}

function trimCommentsToFileLimit(comments) {
    const trimmed = [...comments];
    let serialized = serializeComments(trimmed);

    while (trimmed.length > 0 && Buffer.byteLength(serialized, "utf8") > MAX_COMMENTS_FILE_SIZE) {
        trimmed.pop();
        serialized = serializeComments(trimmed);
    }

    return { comments: trimmed, serialized };
}

function queueCommentWrite(operation) {
    const result = commentsWriteQueue.then(operation);
    commentsWriteQueue = result.catch(() => {});
    return result;
}

queueCommentWrite(async () => {
    const fileStats = await fs.promises.stat(COMMENTS_FILE);
    if (fileStats.size > MAX_COMMENTS_FILE_SIZE) {
        const trimmed = trimCommentsToFileLimit(await readComments());
        await fs.promises.writeFile(COMMENTS_FILE, trimmed.serialized, "utf8");
    }
}).catch((error) => console.error("Failed to clean comments file:", error));

app.get("/api/comments", async (req, res) => {
    try {
        const comments = await commentsWriteQueue.then(readComments);
        res.json(comments.slice(0, 10));
    } catch (error) {
        console.error("Failed to read comments:", error);
        res.status(500).json({ error: "Не удалось загрузить отзывы." });
    }
});

app.post("/api/comments", async (req, res) => {
    const ip = getClientIp(req);

    if (isIpBlocked(ip)) {
        return res.status(429).json({ error: "Слишком много запросов. Попробуйте позже." });
    }

    activeCommentRequests += 1;
    if (activeCommentRequests > MAX_ACTIVE_COMMENT_REQUESTS) {
        activeCommentRequests -= 1;
        for (const [blockedIp, blockedUntil] of blockedIps) {
            if (blockedUntil <= Date.now()) blockedIps.delete(blockedIp);
        }
        blockedIps.set(ip, Date.now() + IP_BLOCK_DURATION_MS);
        return res.status(429).json({ error: "Слишком много одновременных запросов. IP временно заблокирован." });
    }

    try {
        const name = typeof req.body?.name === "string" ? req.body.name.trim() : "";
        const message = typeof req.body?.message === "string" ? req.body.message.trim() : "";

        if (!name || name.length > MAX_NAME_LENGTH || !message || message.length > MAX_COMMENT_LENGTH) {
            return res.status(400).json({
                error: "Имя должно содержать до 10 символов, комментарий — до 45 символов."
            });
        }

        await queueCommentWrite(async () => {
            let comments = await readComments();
            const fileStats = await fs.promises.stat(COMMENTS_FILE);

            if (fileStats.size > MAX_COMMENTS_FILE_SIZE) {
                const trimmed = trimCommentsToFileLimit(comments);
                comments = trimmed.comments;
                await fs.promises.writeFile(COMMENTS_FILE, trimmed.serialized, "utf8");
            }

            comments.unshift({
                id: Date.now(),
                name,
                message,
                avatar: COMMENT_AVATARS[Math.floor(Math.random() * COMMENT_AVATARS.length)],
                date: new Date().toLocaleString()
            });

            const trimmed = trimCommentsToFileLimit(comments);
            await fs.promises.writeFile(COMMENTS_FILE, trimmed.serialized, "utf8");
        });

        res.json({ success: true });
    } catch (error) {
        console.error("Failed to save comment:", error);
        res.status(500).json({ error: "Не удалось сохранить отзыв." });
    } finally {
        activeCommentRequests -= 1;
    }
});

app.listen(PORT, "127.0.0.1", () => {
    console.log(`Server started: http://localhost:${PORT}`);
});
