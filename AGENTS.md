# ipmi-vcd contributor notes

This repository is a laboratory-only IPMI facade for VMware Cloud Director.

- Never commit VCD or IPMI credentials. Runtime credentials come from Kubernetes Secrets.
- Keep the IPMI endpoint pool declarative. Ironic nodes are enrolled against stable endpoint ports.
- A VM is created lazily on the first power-on request and is reused across Ironic deployments.
- Do not automatically delete the VCD VM on power-off or Ironic undeploy.
- Preserve the honest boot limitation: the VCD template must boot from PXE before disk. The emulated
  boot-device command is advisory because the VCD tenant API does not expose VM boot order.
- Run `make check` and `helm lint charts/ipmi-vcd` before committing.
- New commits must use `valerahex@gmail.com` as author and committer email.

