# Build the static site
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Empty base URL: the browser calls /api on the same origin, and nginx proxies it
ENV VITE_API_BASE_URL= \
    VITE_USE_MOCKS=
RUN npm run build

# Serve it with nginx, proxying the API to the backend container
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
