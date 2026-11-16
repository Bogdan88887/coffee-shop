import { Body, Controller, Get, HttpCode, HttpStatus, Ip, Post, Req, Res } from "@nestjs/common";
import { AuthService } from "./auth.service";

@Controller("api/auth")
export class AuthController {
    constructor(private readonly authService: AuthService) {}

    @Post("register")
    @HttpCode(HttpStatus.CREATED)
    async register(@Body() body: any): Promise<{ success: true }> {
        await this.authService.register(body?.username, body?.password);
        return { success: true };
    }

    @Post("login")
    @HttpCode(HttpStatus.OK)
    async login(@Ip() ip: string, @Body() body: any, @Req() request: any, @Res() response: any): Promise<void> {
        const session = await this.authService.login(ip, body?.username, body?.password);
        const secure = request.secure ? "; Secure" : "";
        response.setHeader("Set-Cookie", `coffee_session=${session.token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${session.maxAgeSeconds}${secure}`);
        response.json({ success: true, username: session.username });
    }

    @Get("me")
    getCurrentUser(@Req() request: any): { username: string | null } {
        return { username: this.authService.getUsername(this.readSessionToken(request.headers.cookie)) ?? null };
    }

    @Post("logout")
    @HttpCode(HttpStatus.OK)
    logout(@Req() request: any, @Res() response: any): void {
        this.authService.logout(this.readSessionToken(request.headers.cookie));
        response.setHeader("Set-Cookie", "coffee_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
        response.json({ success: true });
    }

    private readSessionToken(cookieHeader?: string): string | undefined {
        return cookieHeader?.split(";").map((part: string) => part.trim())
            .find((part: string) => part.startsWith("coffee_session="))
            ?.slice("coffee_session=".length);
    }
}
