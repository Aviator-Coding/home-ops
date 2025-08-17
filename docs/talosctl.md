get all otpions `talosctl -n 10.10.3.11 get rd`
```
vscode ➜ /workspaces/home-ops (main) $ talosctl -n 10.10.3.11 get rd
NODE         NAMESPACE   TYPE                 ID                                                 VERSION   ALIASES
10.10.3.11   meta        ResourceDefinition   acquireconfigspecs.v1alpha1.talos.dev              1         acquireconfigspec acs
10.10.3.11   meta        ResourceDefinition   acquireconfigstatuses.v1alpha1.talos.dev           1         acquireconfigstatus acs
10.10.3.11   meta        ResourceDefinition   addressspecs.net.talos.dev                         1         addressspec as
10.10.3.11   meta        ResourceDefinition   addressstatuses.net.talos.dev                      1         address addresses addressstatus as
10.10.3.11   meta        ResourceDefinition   adjtimestatuses.v1alpha1.talos.dev                 1         adjtimestatus as
10.10.3.11   meta        ResourceDefinition   admissioncontrolconfigs.kubernetes.talos.dev       1         admissioncontrolconfig acc accs
10.10.3.11   meta        ResourceDefinition   affiliates.cluster.talos.dev                       1         affiliate
10.10.3.11   meta        ResourceDefinition   apicertificates.secrets.talos.dev                  1         apicertificate ac acs
10.10.3.11   meta        ResourceDefinition   apiserverconfigs.kubernetes.talos.dev              1         apiserverconfig apisc apiscs
10.10.3.11   meta        ResourceDefinition   auditpolicyconfigs.kubernetes.talos.dev            1         auditpolicyconfig apc apcs
10.10.3.11   meta        ResourceDefinition   authorizationconfigs.kubernetes.talos.dev          1         authorizationconfig ac acs
10.10.3.11   meta        ResourceDefinition   blockdevices.block.talos.dev                       1         blockdevice bd bds
10.10.3.11   meta        ResourceDefinition   blocksymlinks.block.talos.dev                      1         blocksymlink bs
10.10.3.11   meta        ResourceDefinition   bootstrapmanifestsconfigs.kubernetes.talos.dev     1         bootstrapmanifestsconfig bmc bmcs
10.10.3.11   meta        ResourceDefinition   certsans.secrets.talos.dev                         1         certsan csan csans
10.10.3.11   meta        ResourceDefinition   configstatuses.kubernetes.talos.dev                1         configstatus cs
10.10.3.11   meta        ResourceDefinition   controllermanagerconfigs.kubernetes.talos.dev      1         controllermanagerconfig cmc cmcs
10.10.3.11   meta        ResourceDefinition   cpustats.perf.talos.dev                            1         cpustat cpus
10.10.3.11   meta        ResourceDefinition   deviceconfigspecs.net.talos.dev                    1         deviceconfigspec dcs
10.10.3.11   meta        ResourceDefinition   devicesstatuses.runtime.talos.dev                  1         devicesstatus ds
10.10.3.11   meta        ResourceDefinition   diagnostics.runtime.talos.dev                      1         diagnostic
10.10.3.11   meta        ResourceDefinition   discoveredvolumes.block.talos.dev                  1         discoveredvolume dv dvs
10.10.3.11   meta        ResourceDefinition   discoveryconfigs.cluster.talos.dev                 1         discoveryconfig dc dcs
10.10.3.11   meta        ResourceDefinition   discoveryrefreshrequests.block.talos.dev           1         discoveryrefreshrequest drr drrs
10.10.3.11   meta        ResourceDefinition   discoveryrefreshstatuses.block.talos.dev           1         discoveryrefreshstatus drs
10.10.3.11   meta        ResourceDefinition   disks.block.talos.dev                              1         disk
10.10.3.11   meta        ResourceDefinition   dnsresolvecaches.net.talos.dev                     1         dnsresolvecach dnsrc dnsrcs
10.10.3.11   meta        ResourceDefinition   dnsupstreams.net.talos.dev                         1         dnsupstream dnsu dnsus
10.10.3.11   meta        ResourceDefinition   endpoints.kubernetes.talos.dev                     1         endpoint
10.10.3.11   meta        ResourceDefinition   etcdconfigs.etcd.talos.dev                         1         etcdconfig ec ecs
10.10.3.11   meta        ResourceDefinition   etcdmembers.etcd.talos.dev                         1         etcdmember em ems
10.10.3.11   meta        ResourceDefinition   etcdrootsecrets.secrets.talos.dev                  1         etcdrootsecret ers
10.10.3.11   meta        ResourceDefinition   etcdsecrets.secrets.talos.dev                      1         etcdsecret es
10.10.3.11   meta        ResourceDefinition   etcdspecs.etcd.talos.dev                           1         etcdspec es
10.10.3.11   meta        ResourceDefinition   etcfilespecs.files.talos.dev                       1         etcfilespec efs
10.10.3.11   meta        ResourceDefinition   etcfilestatuses.files.talos.dev                    1         etcfilestatus efs
10.10.3.11   meta        ResourceDefinition   ethernetspecs.net.talos.dev                        1         ethernetspec es
10.10.3.11   meta        ResourceDefinition   ethernetstatuses.net.talos.dev                     1         ethtool ethernetstatus es
10.10.3.11   meta        ResourceDefinition   eventsinkconfigs.runtime.talos.dev                 1         eventsinkconfig esc escs
10.10.3.11   meta        ResourceDefinition   extensionserviceconfigs.runtime.talos.dev          1         extensionserviceconfig esc escs
10.10.3.11   meta        ResourceDefinition   extensionserviceconfigstatuses.runtime.talos.dev   1         extensionserviceconfigstatus escs
10.10.3.11   meta        ResourceDefinition   extensionstatuses.runtime.talos.dev                1         extensions extensionstatus es
10.10.3.11   meta        ResourceDefinition   extramanifestsconfigs.kubernetes.talos.dev         1         extramanifestsconfig emc emcs
10.10.3.11   meta        ResourceDefinition   hardwareaddresses.net.talos.dev                    1         hardwareaddress ha has
10.10.3.11   meta        ResourceDefinition   hostdnsconfigs.net.talos.dev                       1         hostdnsconfig hdnsc hdnscs
10.10.3.11   meta        ResourceDefinition   hostnamespecs.net.talos.dev                        1         hostnamespec hs
10.10.3.11   meta        ResourceDefinition   hostnamestatuses.net.talos.dev                     1         hostname hostnamestatus hs
10.10.3.11   meta        ResourceDefinition   identities.cluster.talos.dev                       1         identity
10.10.3.11   meta        ResourceDefinition   imagecacheconfigs.cri.talos.dev                    1         imagecacheconfig icc iccs
10.10.3.11   meta        ResourceDefinition   infos.cluster.talos.dev                            1         info
10.10.3.11   meta        ResourceDefinition   kernelmodulespecs.runtime.talos.dev                1         modules kernelmodulespec kms
10.10.3.11   meta        ResourceDefinition   kernelparamdefaultspecs.runtime.talos.dev          1         kernelparamdefaultspec kpds
10.10.3.11   meta        ResourceDefinition   kernelparamspecs.runtime.talos.dev                 1         kernelparamspec kps
10.10.3.11   meta        ResourceDefinition   kernelparamstatuses.runtime.talos.dev              1         sysctls kernelparameters kernelparams kernelparamstatus kps
10.10.3.11   meta        ResourceDefinition   kmsglogconfigs.runtime.talos.dev                   1         kmsglogconfig klc klcs
10.10.3.11   meta        ResourceDefinition   kubeletconfigs.kubernetes.talos.dev                1         kubeletconfig kc kcs
10.10.3.11   meta        ResourceDefinition   kubeletlifecycles.kubernetes.talos.dev             1         kubeletlifecycle kl kls
10.10.3.11   meta        ResourceDefinition   kubeletsecrets.secrets.talos.dev                   1         kubeletsecret ks
10.10.3.11   meta        ResourceDefinition   kubeletspecs.kubernetes.talos.dev                  1         kubeletspec ks
10.10.3.11   meta        ResourceDefinition   kubeprismconfigs.kubernetes.talos.dev              1         kubeprismconfig kpc kpcs
10.10.3.11   meta        ResourceDefinition   kubeprismendpoints.kubernetes.talos.dev            1         kubeprismendpoint kpe kpes
10.10.3.11   meta        ResourceDefinition   kubeprismstatuses.kubernetes.talos.dev             1         kubeprismstatus kps
10.10.3.11   meta        ResourceDefinition   kubernetesaccessconfigs.cluster.talos.dev          1         kubernetesaccessconfig kac kacs
10.10.3.11   meta        ResourceDefinition   kubernetesdynamiccerts.secrets.talos.dev           1         kubernetesdynamiccert kdc kdcs
10.10.3.11   meta        ResourceDefinition   kubernetesrootsecrets.secrets.talos.dev            1         kubernetesrootsecret krs
10.10.3.11   meta        ResourceDefinition   kubernetessecrets.secrets.talos.dev                1         kubernetessecret ks
10.10.3.11   meta        ResourceDefinition   kubespanconfigs.kubespan.talos.dev                 1         kubespanconfig ksc kscs
10.10.3.11   meta        ResourceDefinition   kubespanendpoints.kubespan.talos.dev               1         kubespanendpoint kse kses
10.10.3.11   meta        ResourceDefinition   kubespanidentities.kubespan.talos.dev              1         kubespanidentity ksi ksis
10.10.3.11   meta        ResourceDefinition   kubespanpeerspecs.kubespan.talos.dev               1         kubespanpeerspec ksps
10.10.3.11   meta        ResourceDefinition   kubespanpeerstatuses.kubespan.talos.dev            1         kubespanpeerstatus ksps
10.10.3.11   meta        ResourceDefinition   linkrefreshes.net.talos.dev                        1         linkrefresh lr lrs
10.10.3.11   meta        ResourceDefinition   linkspecs.net.talos.dev                            1         linkspec ls
10.10.3.11   meta        ResourceDefinition   linkstatuses.net.talos.dev                         1         link links linkstatus ls
10.10.3.11   meta        ResourceDefinition   machineconfigs.config.talos.dev                    1         machineconfig mc mcs
10.10.3.11   meta        ResourceDefinition   machineresetsignals.runtime.talos.dev              1         machineresetsignal mrs
10.10.3.11   meta        ResourceDefinition   machinestatuses.runtime.talos.dev                  1         machinestatus ms
10.10.3.11   meta        ResourceDefinition   machinetypes.config.talos.dev                      1         machinetype mt mts
10.10.3.11   meta        ResourceDefinition   maintenancerootsecrets.secrets.talos.dev           1         maintenancerootsecret mrs
10.10.3.11   meta        ResourceDefinition   maintenanceservicecertificates.secrets.talos.dev   1         maintenanceservicecertificate msc mscs
10.10.3.11   meta        ResourceDefinition   maintenanceserviceconfigs.runtime.talos.dev        1         maintenanceserviceconfig msc mscs
10.10.3.11   meta        ResourceDefinition   maintenanceservicerequests.runtime.talos.dev       1         maintenanceservicerequest msr msrs
10.10.3.11   meta        ResourceDefinition   manifests.kubernetes.talos.dev                     1         manifest
10.10.3.11   meta        ResourceDefinition   manifeststatuses.kubernetes.talos.dev              1         manifeststatus ms
10.10.3.11   meta        ResourceDefinition   members.cluster.talos.dev                          1         member
10.10.3.11   meta        ResourceDefinition   memorymodules.hardware.talos.dev                   1         memorymodules ram memorymodule mm mms
10.10.3.11   meta        ResourceDefinition   memorystats.perf.talos.dev                         1         memorystat ms
10.10.3.11   meta        ResourceDefinition   metakeys.runtime.talos.dev                         1         meta metakey mk mks
10.10.3.11   meta        ResourceDefinition   metaloads.runtime.talos.dev                        1         metaload ml mls
10.10.3.11   meta        ResourceDefinition   mountrequests.block.talos.dev                      1         mountrequest mr mrs
10.10.3.11   meta        ResourceDefinition   mountstatuses.block.talos.dev                      1         mountstatus ms
10.10.3.11   meta        ResourceDefinition   mountstatuses.runtime.talos.dev                    1         mounts
10.10.3.11   meta        ResourceDefinition   namespaces.meta.cosi.dev                           1         ns namespace
10.10.3.11   meta        ResourceDefinition   networkstatuses.net.talos.dev                      1         netstatus netstatuses networkstatus ns
10.10.3.11   meta        ResourceDefinition   nftableschains.net.talos.dev                       1         chain chains nftableschain ntc ntcs
10.10.3.11   meta        ResourceDefinition   nodeaddresses.net.talos.dev                        1         nodeaddress na nas
10.10.3.11   meta        ResourceDefinition   nodeaddressfilters.net.talos.dev                   1         nodeaddressfilter naf nafs
10.10.3.11   meta        ResourceDefinition   nodeaddresssortalgorithms.net.talos.dev            1         nodeaddresssortalgorithm nasa nasas
10.10.3.11   meta        ResourceDefinition   nodeannotationspecs.k8s.talos.dev                  1         nodeannotationspec nas
10.10.3.11   meta        ResourceDefinition   nodecordonedspecs.k8s.talos.dev                    1         nodecordonedspec ncs
10.10.3.11   meta        ResourceDefinition   nodeipconfigs.kubernetes.talos.dev                 1         nodeipconfig nipc nipcs
10.10.3.11   meta        ResourceDefinition   nodeips.kubernetes.talos.dev                       1         nodeip nip nips
10.10.3.11   meta        ResourceDefinition   nodelabelspecs.k8s.talos.dev                       1         nodelabelspec nls
10.10.3.11   meta        ResourceDefinition   nodenames.kubernetes.talos.dev                     1         nodename
10.10.3.11   meta        ResourceDefinition   nodestatuses.kubernetes.talos.dev                  1         nodestatus ns
10.10.3.11   meta        ResourceDefinition   nodetaintspecs.k8s.talos.dev                       1         nodetaintspec nts
10.10.3.11   meta        ResourceDefinition   operatorspecs.net.talos.dev                        1         operatorspec os
10.10.3.11   meta        ResourceDefinition   osrootsecrets.secrets.talos.dev                    1         osrootsecret osrs
10.10.3.11   meta        ResourceDefinition   pcidevices.hardware.talos.dev                      1         devices device pcidevice pcid pcids
10.10.3.11   meta        ResourceDefinition   pcidriverrebindconfigs.runtime.talos.dev           1         pcidriverrebindconfig pcidrc pcidrcs
10.10.3.11   meta        ResourceDefinition   pcidriverrebindstatuses.runtime.talos.dev          1         pcidriverrebinds pcidriverrebindstatus pcidrs
10.10.3.11   meta        ResourceDefinition   pcrstatuses.hardware.talos.dev                     1         pcrstatus pcrs
10.10.3.11   meta        ResourceDefinition   pkistatuses.etcd.talos.dev                         1         pkistatus pkis
10.10.3.11   meta        ResourceDefinition   platformmetadatas.talos.dev                        1         platformmetadata pm pms
10.10.3.11   meta        ResourceDefinition   probespecs.net.talos.dev                           1         probespec ps
10.10.3.11   meta        ResourceDefinition   probestatuses.net.talos.dev                        1         probe probes probestatus ps
10.10.3.11   meta        ResourceDefinition   processors.hardware.talos.dev                      1         cpus cpu processor
10.10.3.11   meta        ResourceDefinition   registryconfigs.cri.talos.dev                      1         registryconfig rc rcs
10.10.3.11   meta        ResourceDefinition   resolverspecs.net.talos.dev                        1         resolverspec rs
10.10.3.11   meta        ResourceDefinition   resolverstatuses.net.talos.dev                     1         resolvers resolverstatus rs
10.10.3.11   meta        ResourceDefinition   resourcedefinitions.meta.cosi.dev                  1         api-resources resourcedefinition rd rds
10.10.3.11   meta        ResourceDefinition   routespecs.net.talos.dev                           1         routespec rs
10.10.3.11   meta        ResourceDefinition   routestatuses.net.talos.dev                        1         route routes routestatus rs
10.10.3.11   meta        ResourceDefinition   schedulerconfigs.kubernetes.talos.dev              1         schedulerconfig sc scs
10.10.3.11   meta        ResourceDefinition   seccompprofiles.cri.talos.dev                      1         seccompprofile sp sps
10.10.3.11   meta        ResourceDefinition   secretstatuses.kubernetes.talos.dev                1         secretstatus ss
10.10.3.11   meta        ResourceDefinition   securitystates.talos.dev                           1         securitystate ss
10.10.3.11   meta        ResourceDefinition   services.v1alpha1.talos.dev                        1         svc service
10.10.3.11   meta        ResourceDefinition   siderolinkconfigs.siderolink.talos.dev             1         siderolinkconfig sc scs
10.10.3.11   meta        ResourceDefinition   siderolinkstatuses.siderolink.talos.dev            1         siderolinkstatus ss
10.10.3.11   meta        ResourceDefinition   siderolinktunnels.siderolink.talos.dev             1         siderolinktunnel st sts
10.10.3.11   meta        ResourceDefinition   staticpods.kubernetes.talos.dev                    1         staticpod sp sps
10.10.3.11   meta        ResourceDefinition   staticpodserverstatuses.kubernetes.talos.dev       1         staticpodserverstatus spss
10.10.3.11   meta        ResourceDefinition   staticpodstatuses.kubernetes.talos.dev             1         podstatus staticpodstatus sps
10.10.3.11   meta        ResourceDefinition   systemdisks.block.talos.dev                        1         systemdisk sd sds
10.10.3.11   meta        ResourceDefinition   systeminformations.hardware.talos.dev              1         systeminformation systeminformation si sis
10.10.3.11   meta        ResourceDefinition   timeserverspecs.net.talos.dev                      1         timeserverspec tss
10.10.3.11   meta        ResourceDefinition   timeserverstatuses.net.talos.dev                   1         timeserver timeservers timeserverstatus tss
10.10.3.11   meta        ResourceDefinition   timestatuses.v1alpha1.talos.dev                    1         timestatus ts
10.10.3.11   meta        ResourceDefinition   trustdcertificates.secrets.talos.dev               1         trustdcertificate tc tcs
10.10.3.11   meta        ResourceDefinition   uniquemachinetokens.runtime.talos.dev              1         uniquemachinetoken umt umts
10.10.3.11   meta        ResourceDefinition   userdiskconfigstatuses.block.talos.dev             1         userdiskconfigstatus udcs
10.10.3.11   meta        ResourceDefinition   versions.runtime.talos.dev                         1         version
10.10.3.11   meta        ResourceDefinition   volumeconfigs.block.talos.dev                      1         volumeconfig vc vcs
10.10.3.11   meta        ResourceDefinition   volumelifecycles.block.talos.dev                   1         volumelifecycle vl vls
10.10.3.11   meta        ResourceDefinition   volumemountrequests.block.talos.dev                1         volumemountrequest vmr vmrs
10.10.3.11   meta        ResourceDefinition   volumemountstatuses.block.talos.dev                1         volumemountstatus vms
10.10.3.11   meta        ResourceDefinition   volumestatuses.block.talos.dev                     1         volumestatus vs
10.10.3.11   meta        ResourceDefinition   watchdogtimerconfigs.runtime.talos.dev             1         watchdogtimerconfig wtc wtcs
10.10.3.11   meta        ResourceDefinition   watchdogtimerstatuses.runtime.talos.dev            1         watchdogtimerstatus wts
```
