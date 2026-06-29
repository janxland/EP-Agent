FROM continuumio/miniconda3:latest

ENV DEBIAN_FRONTEND=noninteractive \
    SOVITS_SKIP_BROWSER_OPEN=1 \
    PORT=3333 \
    TERM=xterm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      cmake \
      curl \
      ffmpeg \
      git \
      build-essential \
      libsox-dev \
      nodejs \
      npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json server.js *.html *.sh ./
RUN chmod +x /app/*.sh

EXPOSE 3333 9872 9874

CMD ["node", "server.js"]
