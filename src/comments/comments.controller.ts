import {
    Controller,
    Get,
    HttpCode,
    HttpException,
    HttpStatus,
    Ip,
    Post,
    Body,
} from "@nestjs/common";
import { CommentsService } from "./comments.service";
import { CommentRecord } from "./comment-record";

@Controller("api/comments")
export class CommentsController {
    constructor(private readonly commentsService: CommentsService) {}

    @Get()
    async getComments(): Promise<CommentRecord[]> {
        try {
            return await this.commentsService.getLatestComments();
        } catch (error) {
            console.error("Failed to read comments:", error);
            throw new HttpException(
                { error: "Не удалось загрузить отзывы." },
                HttpStatus.INTERNAL_SERVER_ERROR,
            );
        }
    }

    @Post()
    @HttpCode(HttpStatus.OK)
    async createComment(
        @Ip() ip: string,
        @Body() body: unknown,
    ): Promise<{ success: true }> {
        try {
            await this.commentsService.createComment(ip, body);
            return { success: true };
        } catch (error) {
            if (error instanceof HttpException) throw error;

            console.error("Failed to save comment:", error);
            throw new HttpException(
                { error: "Не удалось сохранить отзыв." },
                HttpStatus.INTERNAL_SERVER_ERROR,
            );
        }
    }
}
