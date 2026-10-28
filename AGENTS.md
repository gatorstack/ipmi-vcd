# ipmi-vcd contributor notes

This repository is a laboratory-only IPMI facade for VMware Cloud Director.

- Never commit VCD or IPMI credentials. Runtime credentials come from Kubernetes Secrets.
- Keep the IPMI endpoint pool declarative. Ironic nodes are enrolled against stable endpoint ports.
- A VM is created lazily on the first power-on request and is reused across Ironic deployments.
- Do not automatically delete the VCD VM on power-off or Ironic undeploy.
- The VCD tenant API does not expose VM boot order. Ironic nodes therefore use the `ipmitool`
  management interface: a `network` boot request arms a one-shot replacement of the powered-off VM's
  system disk from the pristine template before power-on. Other power operations must preserve disks.
- Run `make check` and `helm lint charts/ipmi-vcd` before committing.
- New commits must use `valerahex@gmail.com` as author and committer email.

