import { Inject, Injectable, OnModuleInit, HttpException, HttpStatus } from "@nestjs/common";
import { randomBytes } from "node:crypto";
import { AUTH_PROTECTION, AUTH_REPOSITORY, PASSWORD_HASHER, AuthProtectionPolicy, AuthRepositoryPort, PasswordHasher, UserRecord } from "./auth.types";

interface LoginFailure { count: number; blockedUntil: number; }
interface Session { username: string; expiresAt: number; }

/** Регистрация, проверка пароля и временные сессии приложения. */
@Injectable()
export class AuthService implements OnModuleInit {
    // Сессии хранятся в памяти процесса, поэтому после перезапуска сервера нужен новый вход.
    private readonly sessions = new Map<string, Session>();
    private readonly loginFailures = new Map<string, LoginFailure>();

    constructor(
        @Inject(AUTH_REPOSITORY) private readonly users: AuthRepositoryPort,
        @Inject(PASSWORD_HASHER) private readonly passwordHasher: PasswordHasher,
        @Inject(AUTH_PROTECTION) private readonly protection: AuthProtectionPolicy,
    ) {}

    onModuleInit(): Promise<void> { return this.users.initialize(); }

    async register(usernameInput: unknown, passwordInput: unknown): Promise<void> {
        const username = this.normalizeUsername(usernameInput);
        const password = typeof passwordInput === "string" ? passwordInput : "";
        if (username.length < this.protection.minUsernameLength || username.length > this.protection.maxUsernameLength || !/^[a-z0-9_]+$/.test(username)) {
            throw new HttpException({ error: "Логин: от 3 до 20 латинских букв, цифр или знаков подчёркивания." }, HttpStatus.BAD_REQUEST);
        }
        if (password.length < this.protection.minPasswordLength || password.length > this.protection.maxPasswordLength) {
            throw new HttpException({ error: "Пароль должен содержать от 8 до 128 символов." }, HttpStatus.BAD_REQUEST);
        }
        if (await this.users.findByUsername(username)) {
            throw new HttpException({ error: "Этот логин уже занят." }, HttpStatus.CONFLICT);
        }
        const salt = randomBytes(16).toString("hex");
        const user: UserRecord = {
            username,
            passwordSalt: salt,
            passwordHash: await this.passwordHasher.hash(password, salt),
            createdAt: new Date().toISOString(),
        };
        try {
            await this.users.add(user);
        } catch (error) {
            if (error instanceof Error && error.message.includes("уже зарегистрирован")) {
                throw new HttpException({ error: "Этот логин уже занят." }, HttpStatus.CONFLICT);
            }
            throw error;
        }
    }

    async login(ip: string, usernameInput: unknown, passwordInput: unknown): Promise<{ token: string; username: string; maxAgeSeconds: number }> {
        const now = Date.now();
        const failure = this.loginFailures.get(ip);
        if (failure?.blockedUntil && failure.blockedUntil > now) {
            throw new HttpException({ error: "Слишком много попыток. Попробуйте позже." }, HttpStatus.TOO_MANY_REQUESTS);
        }
        const username = this.normalizeUsername(usernameInput);
        const password = typeof passwordInput === "string" ? passwordInput : "";
        const user = await this.users.findByUsername(username);
        const valid = user ? await this.passwordHasher.verify(password, user.passwordSalt, user.passwordHash) : false;
        // Несколько неверных попыток с одного IP приводят к короткому перерыву перед входом.
        if (!user || !valid) {
            const count = (failure?.count ?? 0) + 1;
            this.loginFailures.set(ip, {
                count: count >= this.protection.maxLoginAttempts ? 0 : count,
                blockedUntil: count >= this.protection.maxLoginAttempts ? now + this.protection.loginBlockDurationMs : 0,
            });
            throw new HttpException({ error: "Неверный логин или пароль." }, HttpStatus.UNAUTHORIZED);
        }
        this.loginFailures.delete(ip);
        const token = randomBytes(32).toString("hex");
        this.sessions.set(token, { username: user.username, expiresAt: now + this.protection.sessionDurationMs });
        return {
            token,
            username: user.username,
            maxAgeSeconds: Math.floor(this.protection.sessionDurationMs / 1000),
        };
    }

    getUsername(token?: string): string | undefined {
        if (!token) return undefined;
        const session = this.sessions.get(token);
        if (!session) return undefined;
        if (session.expiresAt <= Date.now()) { this.sessions.delete(token); return undefined; }
        return session.username;
    }

    logout(token?: string): void { if (token) this.sessions.delete(token); }

    private normalizeUsername(value: unknown): string {
        return typeof value === "string" ? value.trim().toLowerCase() : "";
    }
}
