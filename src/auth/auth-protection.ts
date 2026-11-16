import { AuthProtectionPolicy, AUTH_PROTECTION } from "./auth.types";

export { AUTH_PROTECTION };

/** Параметры регистрации, перебора паролей и срока действия сессии. */
export const authProtectionDefaults: AuthProtectionPolicy = {
    minPasswordLength: 8,
    maxPasswordLength: 128,
    minUsernameLength: 3,
    maxUsernameLength: 20,
    maxLoginAttempts: 5,
    loginBlockDurationMs: 15 * 60 * 1000,
    sessionDurationMs: 24 * 60 * 60 * 1000,
};
