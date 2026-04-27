FROM node:18-bullseye-slim

WORKDIR /app

COPY sunlight-shadow/demo/package.json /app/package.json
RUN npm install

COPY sunlight-shadow/demo /app
RUN chmod +x /app/start-demo.sh

EXPOSE 8080

CMD ["/app/start-demo.sh"]
