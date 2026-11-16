export interface UserRecord {
    username: string;
    passwordHash: string;
    passwordSalt: string;
    createdAt: string;
}

export interface AuthProtectionPolicy {
    minPasswordLength: number;
    maxPasswordLength: number;
    minUsernameLength: number;
    maxUsernameLength: number;
    maxLoginAttempts: number;
    loginBlockDurationMs: number;
    sessionDurationMs: number;
}

export const AUTH_REPOSITORY = Symbol("AUTH_REPOSITORY");
export const PASSWORD_HASHER = Symbol("PASSWORD_HASHER");
export const AUTH_PROTECTION = Symbol("AUTH_PROTECTION");

/** Вычислитель пароля можно заменить через NestJS DI, не меняя AuthService. */
export interface PasswordHasher {
    hash(password: string, salt: string): Promise<string>;
    verify(password: string, salt: string, expectedHash: string): Promise<boolean>;
}

/** Хранилище пользователей также заменяется независимо от процесса входа. */
export interface AuthRepositoryPort {
    initialize(): Promise<void>;
    findByUsername(username: string): Promise<UserRecord | undefined>;
    add(user: UserRecord): Promise<void>;
}
