import { Module } from "@nestjs/common";
import { CommentsModule } from "./comments/comments.module";

@Module({
    imports: [CommentsModule],
})
export class AppModule {}
