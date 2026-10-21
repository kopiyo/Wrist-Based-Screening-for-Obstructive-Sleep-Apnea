FROM node:20.20.0-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm ci
COPY apps/web ./apps/web
COPY packages ./packages
WORKDIR /app/apps/web
ARG VITE_API_URL=http://localhost:8000/v1
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_WEB_PUSH_PUBLIC_KEY=
ENV VITE_WEB_PUSH_PUBLIC_KEY=$VITE_WEB_PUSH_PUBLIC_KEY
RUN npm run build

FROM nginx:1.27.5-alpine
ARG VITE_API_URL=http://localhost:8000/v1
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
# Allow the browser to call only the configured API origin (scheme://host:port).
RUN api_origin="$(printf '%s' "$VITE_API_URL" | sed -E 's#^(https?://[^/]+).*#\1#')" \
 && sed -i "s#__API_ORIGIN__#${api_origin}#g" /etc/nginx/conf.d/default.conf
EXPOSE 80
