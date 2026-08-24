# NATS JetStream

NATS JetStream is a distributed messaging system with persistence and
streaming. Default delivery is **at-least-once** with explicit ack, not
exactly-once.

## Components

- **NATS Server**: Core messaging server with JetStream enabled (3 replicas)
- **NACK Controller**: Kubernetes controller for managing Streams, Consumers, KeyValue, and ObjectStores via CRDs

## Architecture

- 3-node cluster for quorum-based consensus
- File-based storage on Ceph block storage (10Gi per node)
- Memory store for ephemeral high-throughput (1Gi per node)
- `topologySpreadConstraints` (`maxSkew: 1`, `whenUnsatisfiable: DoNotSchedule`
  on `kubernetes.io/hostname`) spread replicas; this is not pod anti-affinity

## Usage

The in-repo example Stream is `events` (`streams/example-stream.yaml`), not
`my-stream`.

```yaml
apiVersion: jetstream.nats.io/v1beta2
kind: Stream
metadata:
  name: events
  namespace: database
spec:
  name: events
  subjects:
    - "events.>"
  storage: file
  replicas: 3
  retention: limits
  maxAge: 168h
  maxBytes: 5368709120
  discard: old
```

### Creating a Consumer

```yaml
apiVersion: jetstream.nats.io/v1beta2
kind: Consumer
metadata:
  name: events-durable
  namespace: database
spec:
  streamName: events
  durableName: events-durable
  deliverPolicy: all
  ackPolicy: explicit
  maxDeliver: 5
  ackWait: 30s
```

## Connection

- Client (ClusterIP): `nats://nats.database.svc.cluster.local:4222`
- Cluster/gossip (port 6222) is on the **headless** Service the chart
  creates, not `nats.database.svc.cluster.local:6222`

## Monitoring

- Prometheus metrics available on port 7777
- NATS dashboard available in Grafana (gnetId 16256)

## References

- [NATS Documentation](https://docs.nats.io/)
- [NATS Helm Charts](https://github.com/nats-io/k8s)
- [NACK Controller](https://github.com/nats-io/nack)
