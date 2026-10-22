import { Injectable, HttpException, HttpStatus, OnModuleInit } from "@nestjs/common";
import { CommentsRepository } from "./comments.repository";
import { CommentRecord } from "./comment-record";

const MAX_ACTIVE_COMMENT_REQUESTS = 30;
const IP_BLOCK_DURATION_MS = 15 * 60 * 1000;
const MAX_COMMENT_LENGTH = 45;
const MAX_NAME_LENGTH = 10;
const COMMENT_AVATARS = Array.from(
    { length: 10 },
    (_, index) => `img/avatars/avatar-${String(index + 1).padStart(2, "0")}.svg`,
);

@Injectable()
export class CommentsService implements OnModuleInit {
    private activeCommentRequests = 0;
    private readonly blockedIps = new Map<string, number>();

    constructor(private readonly commentsRepository: CommentsRepository) {}

    async onModuleInit(): Promise<void> {
        await this.commentsRepository.initialize();
    }

    getLatestComments(): Promise<CommentRecord[]> {
        return this.commentsRepository.getLatest(10);
    }

    async createComment(ip: string, input: unknown): Promise<void> {
        if (this.isIpBlocked(ip)) {
            throw new HttpException(
                { error: "Слишком много запросов. Попробуйте позже." },
                HttpStatus.TOO_MANY_REQUESTS,
            );
        }

        this.activeCommentRequests += 1;
        if (this.activeCommentRequests > MAX_ACTIVE_COMMENT_REQUESTS) {
            this.activeCommentRequests -= 1;
            for (const [blockedIp, blockedUntil] of this.blockedIps) {
                if (blockedUntil <= Date.now()) this.blockedIps.delete(blockedIp);
            }
            this.blockedIps.set(ip, Date.now() + IP_BLOCK_DURATION_MS);
            throw new HttpException(
                { error: "Слишком много одновременных запросов. IP временно заблокирован." },
                HttpStatus.TOO_MANY_REQUESTS,
            );
        }

        try {
            const { name, message } = this.readInput(input);
            if (
                !name ||
                name.length > MAX_NAME_LENGTH ||
                !message ||
                message.length > MAX_COMMENT_LENGTH
            ) {
                throw new HttpException(
                    { error: "Имя должно содержать до 10 символов, комментарий — до 45 символов." },
                    HttpStatus.BAD_REQUEST,
                );
            }

            await this.commentsRepository.add({
                id: Date.now(),
                name,
                message,
                avatar: COMMENT_AVATARS[Math.floor(Math.random() * COMMENT_AVATARS.length)],
                date: new Date().toLocaleString(),
            });
        } finally {
            this.activeCommentRequests -= 1;
        }
    }

    private isIpBlocked(ip: string): boolean {
        const blockedUntil = this.blockedIps.get(ip);
        if (!blockedUntil) return false;
        if (blockedUntil <= Date.now()) {
            this.blockedIps.delete(ip);
            return false;
        }
        return true;
    }

    private readInput(input: unknown): { name: string; message: string } {
        const values = typeof input === "object" && input !== null
            ? (input as Record<string, unknown>)
            : {};
        const name = typeof values.name === "string" ? values.name.trim() : "";
        const message = typeof values.message === "string" ? values.message.trim() : "";
        return { name, message };
    }
}
