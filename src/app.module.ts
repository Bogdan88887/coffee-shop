import { Module } from "@nestjs/common";
import { CommentsModule } from "./comments/comments.module";
import { AuthModule } from "./auth/auth.module";

@Module({
    imports: [AuthModule, CommentsModule],
})
export class AppModule {}
