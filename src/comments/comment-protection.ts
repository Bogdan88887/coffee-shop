/** Настройки защиты отзывов собраны здесь, чтобы менять их без правок бизнес-логики. */
export interface CommentProtectionPolicy {
    maxActiveRequests: number;
    blockDurationMs: number;
    maxCommentLength: number;
    maxNameLength: number;
}

export const COMMENT_PROTECTION = Symbol("COMMENT_PROTECTION");

export const commentProtectionDefaults: CommentProtectionPolicy = {
    maxActiveRequests: 30,
    blockDurationMs: 15 * 60 * 1000,
    maxCommentLength: 45,
    maxNameLength: 20,
};
