import { Module } from "@nestjs/common";
import { AUTH_PROTECTION, AUTH_REPOSITORY, PASSWORD_HASHER } from "./auth.types";
import { authProtectionDefaults } from "./auth-protection";
import { AuthController } from "./auth.controller";
import { AuthService } from "./auth.service";
import { JsonAuthRepository } from "./json-auth.repository";
import { ScryptPasswordHasher } from "./scrypt-password.hasher";

@Module({
    controllers: [AuthController],
    providers: [
        AuthService,
        { provide: AUTH_REPOSITORY, useClass: JsonAuthRepository },
        { provide: PASSWORD_HASHER, useClass: ScryptPasswordHasher },
        { provide: AUTH_PROTECTION, useValue: authProtectionDefaults },
    ],
    exports: [AuthService],
})
export class AuthModule {}
