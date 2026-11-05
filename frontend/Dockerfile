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
# A template: nginx fills in ${MEDIA_BUCKET_URL} when the container starts.
ENV MEDIA_BUCKET_URL=""
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
