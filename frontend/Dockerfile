FROM node:20-alpine

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .

# Public JSON projection from the CLI; never pass auth.json as a build argument.
ARG HYBRO_FRONTEND_CONFIG
ARG HYBRO_BACKEND_URL="http://backend:8000"
ENV HYBRO_FRONTEND_CONFIG=$HYBRO_FRONTEND_CONFIG \
    HYBRO_BACKEND_URL=$HYBRO_BACKEND_URL \
    __NEXT_PROCESSED_ENV=1
RUN npm run build

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
CMD ["npm", "run", "start"]
