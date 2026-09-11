FROM node:20-alpine

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .

# The projection is needed at build time for the compiled server-side API
# rewrite only. Browser settings are served per request from the container's
# HYBRO_FRONTEND_CONFIG (compose), so one image serves every install.
# Never pass auth.json as a build argument.
ARG HYBRO_FRONTEND_CONFIG
ARG HYBRO_BACKEND_URL="http://backend:8000"
ENV HYBRO_FRONTEND_CONFIG=$HYBRO_FRONTEND_CONFIG \
    HYBRO_BACKEND_URL=$HYBRO_BACKEND_URL \
    __NEXT_PROCESSED_ENV=1
RUN npm run build

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
# HYBRO_FRONTEND_CONFIG from ENV above doubles as the runtime default when a
# deployment supplies none; compose overrides it per deployment.
CMD ["npm", "run", "start"]
