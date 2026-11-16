import { Injectable, HttpException, HttpStatus, OnModuleInit } from "@nestjs/common";
import { Inject } from "@nestjs/common";
import { COMMENTS_REPOSITORY, CommentsRepositoryPort } from "./comments.repository";
import { CommentRecord } from "./comment-record";
import { COMMENT_PROTECTION, CommentProtectionPolicy } from "./comment-protection";
const COMMENT_AVATARS = Array.from(
    { length: 10 },
    (_, index) => `img/avatars/avatar-${String(index + 1).padStart(2, "0")}.svg`,
);

@Injectable()
export class CommentsService implements OnModuleInit {
    private activeCommentRequests = 0;
    private readonly blockedIps = new Map<string, number>();

    constructor(
        @Inject(COMMENTS_REPOSITORY) private readonly commentsRepository: CommentsRepositoryPort,
        @Inject(COMMENT_PROTECTION) private readonly protection: CommentProtectionPolicy,
    ) {}

    async onModuleInit(): Promise<void> {
        await this.commentsRepository.initialize();
    }

    getLatestComments(): Promise<CommentRecord[]> {
        return this.commentsRepository.getLatest(10);
    }

    async createComment(ip: string, name: string, input: unknown): Promise<void> {
        // Пока запрос ждёт очередь записи JSON, он считается активным.
        // Если лимит превышен, новый запрос отклоняется, а IP получает временную блокировку.
        if (this.isIpBlocked(ip)) {
            throw new HttpException(
                { error: "Слишком много запросов. Попробуйте позже." },
                HttpStatus.TOO_MANY_REQUESTS,
            );
        }

        this.activeCommentRequests += 1;
        if (this.activeCommentRequests > this.protection.maxActiveRequests) {
            this.activeCommentRequests -= 1;
            for (const [blockedIp, blockedUntil] of this.blockedIps) {
                if (blockedUntil <= Date.now()) this.blockedIps.delete(blockedIp);
            }
            this.blockedIps.set(ip, Date.now() + this.protection.blockDurationMs);
            throw new HttpException(
                { error: "Слишком много одновременных запросов. IP временно заблокирован." },
                HttpStatus.TOO_MANY_REQUESTS,
            );
        }

        try {
            const { message } = this.readInput(input);
            if (
                !name ||
                name.length > this.protection.maxNameLength ||
                !message ||
                message.length > this.protection.maxCommentLength
            ) {
                throw new HttpException(
                    { error: `Имя должно содержать до ${this.protection.maxNameLength} символов, комментарий — до ${this.protection.maxCommentLength} символов.` },
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
        // Удаляем истёкшую блокировку при следующем запросе с этого IP.
        // Карта хранит только действующие ограничения для адресов, которые вернулись на сайт.
        if (blockedUntil <= Date.now()) {
            this.blockedIps.delete(ip);
            return false;
        }
        return true;
    }

    private readInput(input: unknown): { message: string } {
        const values = typeof input === "object" && input !== null
            ? (input as Record<string, unknown>)
            : {};
        const message = typeof values.message === "string" ? values.message.trim() : "";
        return { message };
    }
}
