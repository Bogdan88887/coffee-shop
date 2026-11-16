import { Injectable } from "@nestjs/common";
import { promisify } from "node:util";
import { scrypt as scryptCallback, timingSafeEqual } from "node:crypto";
import { PasswordHasher } from "./auth.types";

const scrypt = promisify(scryptCallback);

/** Scrypt с индивидуальной солью работает на встроенных модулях Node.js. */
@Injectable()
export class ScryptPasswordHasher implements PasswordHasher {
    async hash(password: string, salt: string): Promise<string> {
        // Для каждого пользователя AuthService создаёт случайную соль.
        // В файл попадает только результат scrypt, сам пароль не сохраняется.
        const key = await scrypt(password, salt, 64) as Buffer;
        return key.toString("hex");
    }

    async verify(password: string, salt: string, expectedHash: string): Promise<boolean> {
        const actual = Buffer.from(await this.hash(password, salt), "hex");
        const expected = Buffer.from(expectedHash, "hex");
        // Сравнение постоянного времени уменьшает утечки по задержке проверки пароля.
        return actual.length === expected.length && timingSafeEqual(actual, expected);
    }
}
