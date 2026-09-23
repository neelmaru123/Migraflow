import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Read authentication cookies set by backend API or client
  const accessToken = request.cookies.get('access_token')?.value;
  const refreshToken = request.cookies.get('refresh_token')?.value;
  const loggedInCookie = request.cookies.get('logged_in')?.value;

  const hasToken = Boolean(accessToken || refreshToken);
  const isAuthenticated = Boolean(hasToken || loggedInCookie === 'true');

  // Route classifications
  const isPublicAuthRoute = pathname === '/' || pathname === '/login' || pathname === '/register';

  const isProtectedRoute =
    pathname.startsWith('/dashboard') ||
    pathname.startsWith('/sources') ||
    pathname.startsWith('/agents') ||
    pathname.startsWith('/transformation-plan') ||
    pathname.startsWith('/execution') ||
    pathname.startsWith('/profiling');

  // 1. Signed-in users: Redirect away from landing/login/register to /dashboard ONLY if they actually have a token
  if (hasToken && isPublicAuthRoute) {
    return NextResponse.redirect(new URL('/dashboard', request.url));
  }

  // 2. Unauthenticated users: Prevent access to protected app routes & redirect to /login
  if (!isAuthenticated && isProtectedRoute) {
    const loginUrl = new URL('/login', request.url);
    if (pathname !== '/dashboard') {
      loginUrl.searchParams.set('redirect', pathname);
    }
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public assets (/images, etc.)
     * - API routes (/api)
     */
    '/((?!_next/static|_next/image|favicon.ico|api|images).*)',
  ],
};
