export interface CommentRecord {
    id: number;
    name: string;
    message: string;
    avatar?: string;
    date: string;
    [key: string]: unknown;
}
