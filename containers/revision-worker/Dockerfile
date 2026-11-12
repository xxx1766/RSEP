
FROM golang:1.26.2-bookworm AS build

WORKDIR /src
COPY go.mod go.sum ./
COPY proto/go.mod proto/go.sum ./proto/
RUN go mod download

COPY . .
RUN mkdir -p /out/tmp && chmod 1777 /out/tmp \
    && CGO_ENABLED=1 GOOS=linux GOARCH=amd64 \
       go build -trimpath -buildvcs=false \
       -ldflags '-linkmode external -extldflags "-static"' \
       -o /out/revision-worker ./cmd/revision-worker \
    && CGO_ENABLED=1 GOOS=linux GOARCH=amd64 \
       go build -trimpath -buildvcs=false \
       -ldflags '-linkmode external -extldflags "-static"' \
       -o /out/revision-domain-worker ./cmd/revision-domain-worker \
    && CGO_ENABLED=1 GOOS=linux GOARCH=amd64 \
       go build -trimpath -buildvcs=false \
       -ldflags '-linkmode external -extldflags "-static"' \
       -o /out/revision-cluster-worker ./cmd/revision-cluster-worker

FROM scratch AS worker
COPY --from=build /out/tmp /tmp
COPY --from=build /out/revision-worker /worker
ENV PATH=/ \
    GOMAXPROCS=1
ENTRYPOINT ["/worker"]
CMD ["--mode=paix"]

FROM scratch AS domain
COPY --from=build /out/tmp /tmp
COPY --from=build /out/revision-domain-worker /domain-worker
ENV PATH=/ \
    GOMAXPROCS=1
ENTRYPOINT ["/domain-worker"]
CMD ["--mode=paix"]

FROM scratch AS cluster
COPY --from=build /out/tmp /tmp
COPY --from=build /out/revision-cluster-worker /worker
ENV PATH=/ GOMAXPROCS=1
ENTRYPOINT ["/worker"]
CMD ["--mode=paix"]
