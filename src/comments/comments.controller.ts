import {
    Controller,
    Get,
    HttpCode,
    HttpException,
    HttpStatus,
    Ip,
    Post,
    Body,
    Req,
    UnauthorizedException,
} from "@nestjs/common";
import { CommentsService } from "./comments.service";
import { CommentRecord } from "./comment-record";
import { AuthService } from "../auth/auth.service";

@Controller("api/comments")
export class CommentsController {
    constructor(
        private readonly commentsService: CommentsService,
        private readonly authService: AuthService,
    ) {}

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
        @Req() request: any,
    ): Promise<{ success: true }> {
        try {
            const username = this.authService.getUsername(this.readSessionToken(request.headers.cookie));
            if (!username) throw new UnauthorizedException({ error: "Войдите в аккаунт, чтобы оставить отзыв." });
            await this.commentsService.createComment(ip, username, body);
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

    private readSessionToken(cookieHeader?: string): string | undefined {
        const sessionCookie = cookieHeader?.split(";").map((part: string) => part.trim())
            .find((part: string) => part.startsWith("coffee_session="));
        return sessionCookie?.slice("coffee_session=".length);
    }
}
