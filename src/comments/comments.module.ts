import { Module } from "@nestjs/common";
import { CommentsController } from "./comments.controller";
import { COMMENTS_REPOSITORY, JsonCommentsRepository } from "./comments.repository";
import { CommentsService } from "./comments.service";
import { COMMENT_PROTECTION, commentProtectionDefaults } from "./comment-protection";
import { AuthModule } from "../auth/auth.module";

@Module({
    controllers: [CommentsController],
    imports: [AuthModule],
    providers: [
        CommentsService,
        { provide: COMMENTS_REPOSITORY, useClass: JsonCommentsRepository },
        { provide: COMMENT_PROTECTION, useValue: commentProtectionDefaults },
    ],
})
export class CommentsModule {}
