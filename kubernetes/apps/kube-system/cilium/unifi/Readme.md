# BGP Configuration Guide

This document explains the BGP (Border Gateway Protocol) configuration for establishing peering with Kubernetes nodes.

## Configuration Overview

This BGP configuration establishes a peering relationship between a router (AS 64513) and Kubernetes nodes (AS 64514) for route advertisement and network connectivity.

## Detailed Configuration Breakdown

### Router Basic Configuration

```
router bgp 64513
```
- **Purpose**: Initiates BGP process and defines the local Autonomous System (AS) number
- **AS 64513**: Private AS number (range 64512-65534) used for internal networks
- **Effect**: Creates the BGP routing process instance

```
bgp router-id 10.10.3.1
```
- **Purpose**: Sets a unique identifier for this BGP router
- **Value**: 10.10.3.1 (typically matches a loopback or stable interface IP)
- **Importance**: Used for BGP session identification and loop prevention

### BGP Policy Configuration

```
no bgp ebgp-requires-policy
```
- **Purpose**: Disables the requirement for explicit route policies on eBGP sessions
- **Default Behavior**: Modern BGP implementations require explicit policies
- **Effect**: Allows routes to be advertised/received without route-maps or prefix-lists
- **⚠️ Security Note**: Use with caution in production environments

```
no bgp default ipv4-unicast
```
- **Purpose**: Disables automatic IPv4 unicast address family activation
- **Benefit**: Provides explicit control over which address families are active
- **Best Practice**: Prevents accidental route advertisement before proper configuration

```
no bgp network import-check
```
- **Purpose**: Disables route existence verification in local routing table
- **Effect**: Allows advertising networks not present in the local RIB
- **Use Case**: Useful for route aggregation or redistribution scenarios

### Peer Group Configuration

```
neighbor k8s peer-group
```
- **Purpose**: Creates a template for multiple neighbors with identical configuration
- **Benefit**: Simplifies configuration and reduces errors for similar peers
- **Name**: "k8s" indicates these are Kubernetes node peers

```
neighbor k8s remote-as 64514
```
- **Purpose**: Defines the AS number of the peer group members
- **AS 64514**: Private AS assigned to Kubernetes nodes
- **Relationship**: eBGP (External BGP) since local AS (64513) ≠ remote AS (64514)

### BGP Timers Configuration

```
neighbor k8s timers 10 30
```
- **Keepalive**: 10 seconds (how often keepalive messages are sent)
- **Hold Timer**: 30 seconds (time to wait before declaring neighbor down)
- **⚠️ Aggressive**: Default is typically 60/180 seconds
- **Trade-off**: Faster convergence vs. increased CPU/bandwidth usage

```
neighbor k8s timers connect 30
```
- **Purpose**: Sets BGP connection retry timer to 30 seconds
- **Effect**: Time to wait before attempting to re-establish a failed BGP session
- **Default**: Usually 120 seconds

### Session Configuration

```
neighbor k8s activate
```
- **Purpose**: Enables the BGP session for the peer group
- **Note**: This appears to be outside address-family (legacy command)
- **Modern Equivalent**: Should be under `address-family ipv4 unicast`

```
neighbor k8s soft-reconfiguration inbound
```
- **Purpose**: Stores unmodified routes received from peers
- **Benefit**: Allows policy changes without session reset
- **Memory Impact**: Increases memory usage but improves operational flexibility

### Individual Peer Configuration

```
neighbor 10.10.3.11 peer-group k8s
neighbor 10.10.3.12 peer-group k8s
neighbor 10.10.3.13 peer-group k8s
```
- **Purpose**: Assigns specific IP addresses to the k8s peer group template
- **IPs**: Kubernetes node addresses (10.10.3.11, 10.10.3.12, 10.10.3.13)
- **Inheritance**: Each neighbor inherits all k8s peer-group settings

### Address Family Configuration

```
address-family ipv4 unicast
```
- **Purpose**: Enters IPv4 unicast address family configuration mode
- **Scope**: All subsequent commands apply only to IPv4 unicast routing

```
redistribute connected
```
- **Purpose**: Advertises directly connected network routes into BGP
- **Effect**: Makes local subnets reachable from Kubernetes nodes
- **Use Case**: Essential for pod-to-external-service connectivity

```
neighbor k8s next-hop-self
```
- **Purpose**: Sets this router as the next-hop for routes advertised to k8s peers
- **Benefit**: Simplifies routing by avoiding multi-hop scenarios
- **Requirement**: Essential when this router acts as a gateway

```
neighbor k8s activate
```
- **Purpose**: Activates IPv4 unicast address family for k8s peer group
- **Requirement**: Necessary for route exchange in this address family

```
exit-address-family
```
- **Purpose**: Exits the IPv4 unicast address family configuration mode
- **Effect**: Returns to global BGP configuration mode

## Network Topology

```
┌─────────────────┐    eBGP     ┌─────────────────┐
│   This Router   │◄──────────► │ Kubernetes Node │
│   AS 64513      │             │   AS 64514      │
│  10.10.3.1      │             │  10.10.3.11     │
└─────────────────┘             └─────────────────┘
                                ┌─────────────────┐
                                │ Kubernetes Node │
                                │   AS 64514      │
                                │  10.10.3.12     │
                                └─────────────────┘
                                ┌─────────────────┐
                                │ Kubernetes Node │
                                │   AS 64514      │
                                │  10.10.3.13     │
                                └─────────────────┘
```

## Security Considerations

1. **Private AS Numbers**: Using private AS range (64512-65535) for internal networks
2. **No Policy Requirement**: `no bgp ebgp-requires-policy` reduces security - consider implementing explicit policies
3. **Aggressive Timers**: Fast convergence but increased resource usage
4. **Route Redistribution**: `redistribute connected` exposes local networks

## Troubleshooting Commands

```bash
# Check BGP summary
show ip bgp summary

# Verify neighbor status
show ip bgp neighbors

# Check received routes
show ip bgp neighbors 10.10.3.11 received-routes

# Verify advertised routes
show ip bgp neighbors 10.10.3.11 advertised-routes

# Check BGP table
show ip bgp
```

## Best Practices

1. **Implement Route Filtering**: Use prefix-lists or route-maps for security
2. **Monitor BGP Sessions**: Set up alerts for session state changes
3. **Document Changes**: Maintain configuration change logs
4. **Test Failover**: Verify behavior when individual peers fail
5. **Review Timers**: Consider less aggressive timers for production stability
