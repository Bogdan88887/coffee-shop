import { Injectable } from "@nestjs/common";
import { promises as fs } from "node:fs";
import { join } from "node:path";
import { CommentRecord } from "./comment-record";

const MAX_COMMENTS_FILE_SIZE = 3 * 1024 * 1024;

/** Контракт хранилища отзывов: сервис знает только эти операции, а не формат файла. */
export interface CommentsRepositoryPort {
    initialize(): Promise<void>;
    getLatest(limit: number): Promise<CommentRecord[]>;
    add(comment: CommentRecord): Promise<void>;
}

export const COMMENTS_REPOSITORY = Symbol("COMMENTS_REPOSITORY");

@Injectable()
export class JsonCommentsRepository implements CommentsRepositoryPort {
    private readonly commentsFile = join(process.cwd(), "comments.json");
    private commentsWriteQueue: Promise<unknown> = Promise.resolve();

    async initialize(): Promise<void> {
        try {
            await fs.access(this.commentsFile);
        } catch {
            await fs.writeFile(this.commentsFile, "[]", "utf8");
        }

        await this.queueWrite(async () => {
            const fileStats = await fs.stat(this.commentsFile);
            if (fileStats.size > MAX_COMMENTS_FILE_SIZE) {
                const trimmed = this.trimToFileLimit(await this.readAll());
                await fs.writeFile(this.commentsFile, trimmed.serialized, "utf8");
            }
        });
    }

    async getLatest(limit: number): Promise<CommentRecord[]> {
        const comments = await this.commentsWriteQueue.then(() => this.readAll());
        return comments.slice(0, limit);
    }

    add(comment: CommentRecord): Promise<void> {
        return this.queueWrite(async () => {
            let comments = await this.readAll();
            const fileStats = await fs.stat(this.commentsFile);

            if (fileStats.size > MAX_COMMENTS_FILE_SIZE) {
                const trimmed = this.trimToFileLimit(comments);
                comments = trimmed.comments;
                await fs.writeFile(this.commentsFile, trimmed.serialized, "utf8");
            }

            comments.unshift(comment);
            const trimmed = this.trimToFileLimit(comments);
            await fs.writeFile(this.commentsFile, trimmed.serialized, "utf8");
        });
    }

    private async readAll(): Promise<CommentRecord[]> {
        const contents = await fs.readFile(this.commentsFile, "utf8");
        const comments: unknown = JSON.parse(contents);
        return Array.isArray(comments) ? comments as CommentRecord[] : [];
    }

    private trimToFileLimit(comments: CommentRecord[]): {
        comments: CommentRecord[];
        serialized: string;
    } {
        // Новые отзывы находятся в начале массива, поэтому удаление с конца сохраняет их первыми.
        // Так JSON остаётся ниже лимита, а самые свежие отзывы остаются доступными.
        const trimmed = [...comments];
        let serialized = JSON.stringify(trimmed, null, 2);

        while (
            trimmed.length > 0 &&
            Buffer.byteLength(serialized, "utf8") > MAX_COMMENTS_FILE_SIZE
        ) {
            trimmed.pop();
            serialized = JSON.stringify(trimmed, null, 2);
        }

        return { comments: trimmed, serialized };
    }

    private queueWrite<T>(operation: () => Promise<T>): Promise<T> {
        const result = this.commentsWriteQueue.then(operation);
        this.commentsWriteQueue = result.catch(() => undefined);
        return result;
    }
}
