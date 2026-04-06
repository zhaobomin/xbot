# Build stage
FROM rust:1.75-slim as builder

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy manifests
COPY Cargo.toml Cargo.lock ./
COPY crates/acp-client/Cargo.toml ./crates/acp-client/
COPY crates/feishu-channel/Cargo.toml ./crates/feishu-channel/
COPY crates/app/Cargo.toml ./crates/app/

# Create dummy main.rs to build dependencies
RUN mkdir -p crates/acp-client/src crates/feishu-channel/src crates/app/src && \
    echo "fn main() {}" > crates/app/src/main.rs && \
    echo "" > crates/acp-client/src/lib.rs && \
    echo "" > crates/feishu-channel/src/lib.rs

# Build dependencies
RUN cargo build --release && rm -rf crates

# Copy source code
COPY crates ./crates

# Build application
RUN cargo build --release

# Runtime stage
FROM debian:bookworm-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy binary
COPY --from=builder /app/target/release/xbot-acp /usr/local/bin/

# Copy config
COPY config ./config

# Create non-root user
RUN useradd -r -s /bin/false xbot && \
    chown -R xbot:xbot /app

USER xbot

# Environment
ENV RUST_LOG=info
ENV XBOT_CONFIG=/app/config/default.toml

ENTRYPOINT ["xbot-acp"]
CMD ["--help"]