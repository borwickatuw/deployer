# Route53

Creates DNS records in a Route 53 hosted zone. Supports A (alias) records and CNAME records.

## Usage

```hcl
module "dns" {
  source = "../../modules/route53"

  zone_id = aws_route53_zone.main.zone_id
  records = {
    main = {
      type = "A"
      name = "myapp-staging.example.com"
      alias_target = {
        dns_name = module.alb.dns_name
        zone_id  = module.alb.zone_id
      }
    }
    www = {
      type        = "CNAME"
      name        = "www.myapp.example.com"
      cname_value = "myapp.example.com"
    }
  }
}
```

## Key Variables

| Variable | Type        | Description                                                         |
| -------- | ----------- | ------------------------------------------------------------------- |
| zone_id  | string      | Route 53 hosted zone ID                                             |
| records  | map(object) | Map of DNS records (A with alias_target, or CNAME with cname_value) |

## Outputs

| Output       | Description                 |
| ------------ | --------------------------- |
| record_fqdns | Map of record keys to FQDNs |
| record_names | Map of record keys to names |
