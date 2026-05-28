# Multi-stage Dockerfile shared by all FlowGreeks binaries.
#
# Build:
#   docker build --build-arg BINARY=api -t flowgreeks/api .
#   docker build --build-arg BINARY=ingest -t flowgreeks/ingest .
#   docker build --build-arg BINARY=compute -t flowgreeks/compute .
#   docker build --build-arg BINARY=replay -t flowgreeks/replay .
#
# All images are scratch-based with the static binary + ca-certs only.

ARG GO_VERSION=1.25

FROM golang:${GO_VERSION}-alpine AS build
ARG BINARY
RUN apk add --no-cache ca-certificates git

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build \
        -ldflags="-s -w" \
        -trimpath \
        -o /out/app ./cmd/${BINARY}

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=build /out/app /app
USER nonroot:nonroot
ENTRYPOINT ["/app"]
