"""Add a narrowly scoped TCP ingress rule for the isolated Medify web port."""
import argparse
import os
import oci


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--port", type=int, default=3100)
    args = parser.parse_args()
    config = oci.config.from_file(os.path.expanduser("~/.oci/config"))
    compute = oci.core.ComputeClient(config)
    network = oci.core.VirtualNetworkClient(config)
    instance_id = open(os.path.expanduser("~/.oci/pickly_instance.txt"), encoding="utf-8").read().strip()
    attachments = compute.list_vnic_attachments(config["tenancy"], instance_id=instance_id).data
    if not attachments:
        raise RuntimeError("No VNIC attachment found")
    vnic = network.get_vnic(attachments[0].vnic_id).data
    subnet = network.get_subnet(vnic.subnet_id).data
    changed = 0
    for security_list_id in subnet.security_list_ids:
        response = network.get_security_list(security_list_id)
        security_list = response.data
        exists = any(
            rule.protocol == "6"
            and rule.tcp_options
            and rule.tcp_options.destination_port_range
            and rule.tcp_options.destination_port_range.min <= args.port <= rule.tcp_options.destination_port_range.max
            for rule in security_list.ingress_security_rules
        )
        print(f"{security_list.display_name}: port {args.port} {'already open' if exists else 'not open'}")
        if args.apply and not exists:
            rule = oci.core.models.IngressSecurityRule(
                protocol="6",
                source="0.0.0.0/0",
                source_type="CIDR_BLOCK",
                description="Medify isolated web service",
                is_stateless=False,
                tcp_options=oci.core.models.TcpOptions(
                    destination_port_range=oci.core.models.PortRange(min=args.port, max=args.port)
                ),
            )
            details = oci.core.models.UpdateSecurityListDetails(
                display_name=security_list.display_name,
                ingress_security_rules=[*security_list.ingress_security_rules, rule],
                egress_security_rules=security_list.egress_security_rules,
            )
            network.update_security_list(security_list.id, details, if_match=response.headers.get("etag"))
            changed += 1
    print(f"changed={changed}")


if __name__ == "__main__":
    main()
