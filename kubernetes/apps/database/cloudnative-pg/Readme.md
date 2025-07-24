Manual backuos can be done with

```
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: manual-backup-postgres-cluster-16
  namespace: database
spec:
  cluster:
    name: postgres-16
```
