# ipmi-vcd contributor notes

This repository is a laboratory-only IPMI facade for VMware Cloud Director.

- Never commit VCD or IPMI credentials. Runtime credentials come from Kubernetes Secrets.
- Keep the IPMI endpoint pool declarative. Ironic nodes are enrolled against stable endpoint ports.
- A VM is created lazily on the first power-on request and is reused across Ironic deployments.
- Do not automatically delete the VCD VM on power-off or Ironic undeploy.
- Preserve the honest boot limitation: the emulated boot-device command is advisory because the VCD
  tenant API does not expose VM boot order. The verified study template uses a blank disk so firmware
  falls through to PXE before deployment and boots that disk after Ironic writes the image.
- Run `make check` and `helm lint charts/ipmi-vcd` before committing.
- New commits must use `valerahex@gmail.com` as author and committer email.

