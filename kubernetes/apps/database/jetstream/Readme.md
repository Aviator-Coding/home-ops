# NATS JetStream

NATS JetStream is a distributed messaging system with persistence, exactly-once semantics, and streaming capabilities.

## Components

- **NATS Server**: Core messaging server with JetStream enabled (3 replicas)
- **NACK Controller**: Kubernetes controller for managing Streams, Consumers, KeyValue, and ObjectStores via CRDs

## Architecture

- 3-node cluster for quorum-based consensus
- File-based storage on Ceph block storage (10Gi per node)
- Memory store for ephemeral high-throughput (1Gi per node)
- Pod anti-affinity ensures one replica per Kubernetes node

## Usage

### Creating a Stream

```yaml
apiVersion: jetstream.nats.io/v1beta2
kind: Stream
metadata:
  name: my-stream
  namespace: database
spec:
  name: my-stream
  subjects:
    - "my.subject.>"
  storage: file
  replicas: 3
  retention: limits
  maxAge: 168h
```

### Creating a Consumer

```yaml
apiVersion: jetstream.nats.io/v1beta2
kind: Consumer
metadata:
  name: my-consumer
  namespace: database
spec:
  streamName: my-stream
  durableName: my-consumer
  deliverPolicy: all
  ackPolicy: explicit
  maxDeliver: 5
  ackWait: 30s
```

## Connection

- Internal: `nats://nats.database.svc.cluster.local:4222`
- Cluster: `nats://nats.database.svc.cluster.local:6222`

## Monitoring

- Prometheus metrics available on port 7777
- NATS dashboard available in Grafana

## References

- [NATS Documentation](https://docs.nats.io/)
- [NATS Helm Charts](https://github.com/nats-io/k8s)
- [NACK Controller](https://github.com/nats-io/nack)
