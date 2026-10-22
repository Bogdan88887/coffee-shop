import "reflect-metadata";
import { NestFactory } from "@nestjs/core";
import { NestExpressApplication } from "@nestjs/platform-express";
import { json } from "express";
import { join } from "node:path";
import { AppModule } from "./app.module";

async function bootstrap(): Promise<void> {
    const app = await NestFactory.create<NestExpressApplication>(AppModule, {
        bodyParser: false,
    });

    app.set("trust proxy", "loopback");
    app.use(json({ limit: "8kb" }));
    app.useStaticAssets(join(process.cwd(), "public"), { index: "index.html" });
    app.use("/comments.json", (_request, response) => response.sendStatus(404));

    const port = Number(process.env.PORT ?? 3000);
    const host = process.env.HOST ?? "127.0.0.1";
    await app.listen(port, host);
    console.log(`NestJS server started: http://${host}:${port}`);
}

void bootstrap();
