FROM node:20-alpine

WORKDIR /app

# Install dependencies based on the preferred package manager
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .

# Build-time frontend configuration.
#
# Next.js inlines every NEXT_PUBLIC_* value into the CLIENT JavaScript bundle
# during `npm run build`. If we pass these only at container start, the browser
# still runs the compiled defaults ('/api/v1', http://localhost:8000, ...).
# Compose forwards the same values as build args so a custom API prefix or
# Clerk key from the repo-root .env actually reaches the browser bundle.
#
# BACKEND_URL is used by next.config.ts to rewrite /api/* to the in-cluster
# backend service; it stays a plain env var since it is only read server-side.
ARG BACKEND_URL="http://backend:8000"
ARG NEXT_PUBLIC_API_BASE_URL=""
ARG NEXT_PUBLIC_API_PREFIX="/api/v1"
ARG NEXT_PUBLIC_SERVER_URL="http://localhost:3000"
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=""
ARG NEXT_PUBLIC_CLERK_SIGN_IN_URL="/sign-in"
ARG NEXT_PUBLIC_CLERK_SIGN_UP_URL="/sign-up"
ARG NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL="/"
ARG NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL="/"
ARG NEXT_PUBLIC_ENABLE_WAITLIST="true"
ARG NEXT_PUBLIC_MAX_MESSAGE_LENGTH="10000"
ARG NEXT_PUBLIC_INSPECTION_TIMEOUT_MS="300000"

ENV BACKEND_URL=$BACKEND_URL \
    NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_PUBLIC_API_PREFIX=$NEXT_PUBLIC_API_PREFIX \
    NEXT_PUBLIC_SERVER_URL=$NEXT_PUBLIC_SERVER_URL \
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY \
    NEXT_PUBLIC_CLERK_SIGN_IN_URL=$NEXT_PUBLIC_CLERK_SIGN_IN_URL \
    NEXT_PUBLIC_CLERK_SIGN_UP_URL=$NEXT_PUBLIC_CLERK_SIGN_UP_URL \
    NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=$NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL \
    NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=$NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL \
    NEXT_PUBLIC_ENABLE_WAITLIST=$NEXT_PUBLIC_ENABLE_WAITLIST \
    NEXT_PUBLIC_MAX_MESSAGE_LENGTH=$NEXT_PUBLIC_MAX_MESSAGE_LENGTH \
    NEXT_PUBLIC_INSPECTION_TIMEOUT_MS=$NEXT_PUBLIC_INSPECTION_TIMEOUT_MS

RUN npm run build

# Expose port
EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["npm", "run", "start"]
