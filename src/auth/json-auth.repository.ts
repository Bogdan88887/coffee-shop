import { Injectable } from "@nestjs/common";
import { promises as fs } from "node:fs";
import { join } from "node:path";
import { AuthRepositoryPort, UserRecord } from "./auth.types";

/** Пользовательские записи хранятся в отдельном JSON-файле с очередью записи. */
@Injectable()
export class JsonAuthRepository implements AuthRepositoryPort {
    private readonly usersFile = join(process.cwd(), "users.json");
    private writeQueue: Promise<unknown> = Promise.resolve();

    async initialize(): Promise<void> {
        try {
            await fs.access(this.usersFile);
        } catch {
            await fs.writeFile(this.usersFile, "[]", { encoding: "utf8", flag: "wx", mode: 0o600 }).catch(async (error: NodeJS.ErrnoException) => {
                if (error.code !== "EEXIST") throw error;
            });
        }
    }

    async findByUsername(username: string): Promise<UserRecord | undefined> {
        return this.writeQueue.then(async () => (await this.readAll()).find((user) => user.username === username));
    }

    add(user: UserRecord): Promise<void> {
        // Записи пользователей выполняются по очереди, чтобы параллельные регистрации
        // не перезаписали JSON-файл и не создали два аккаунта с одним логином.
        const result = this.writeQueue.then(async () => {
            const users = await this.readAll();
            if (users.some((saved) => saved.username === user.username)) {
                throw new Error("Пользователь уже зарегистрирован.");
            }
            users.push(user);
            await fs.writeFile(this.usersFile, JSON.stringify(users, null, 2), "utf8");
        });
        this.writeQueue = result.catch(() => undefined);
        return result;
    }

    private async readAll(): Promise<UserRecord[]> {
        const parsed: unknown = JSON.parse(await fs.readFile(this.usersFile, "utf8"));
        if (!Array.isArray(parsed)) throw new Error("Файл пользователей должен содержать JSON-массив.");
        return parsed as UserRecord[];
    }
}
