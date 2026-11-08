# The bundle is plain static files, so it is built natively even when the
# image targets another architecture; only the nginx stage follows the target.
FROM --platform=$BUILDPLATFORM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
# Reproducible install once package-lock.json is committed.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# A template: nginx fills in ${MEDIA_BUCKET_URL} and ${RATE_LIMIT_EXEMPT} when the container starts.
ENV MEDIA_BUCKET_URL="" \
    RATE_LIMIT_EXEMPT=127.0.0.1/32
COPY nginx.conf /etc/nginx/templates/default.conf.template
# The image allows 1024 connections per worker, and a WebSocket takes two of them (browser
# and backend), so two workers would hold about a thousand open tabs. The file limit for
# this is raised in docker-compose.yml.
RUN sed -i 's/worker_connections  1024;/worker_connections 16384;/' /etc/nginx/nginx.conf \
    && grep -q 'worker_connections 16384;' /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
