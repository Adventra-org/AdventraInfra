# Build stage: use Maven to resolve azure-identity-extensions and ALL transitive deps
FROM maven:3.9-eclipse-temurin-21 AS deps

WORKDIR /build
RUN printf '%s\n' \
  '<project>' \
  '  <modelVersion>4.0.0</modelVersion>' \
  '  <groupId>keycloak</groupId>' \
  '  <artifactId>azure-deps</artifactId>' \
  '  <version>1.0</version>' \
  '  <dependencies>' \
  '    <dependency>' \
  '      <groupId>com.azure</groupId>' \
  '      <artifactId>azure-identity-extensions</artifactId>' \
  '      <version>1.2.7</version>' \
  '    </dependency>' \
  '  </dependencies>' \
  '</project>' > pom.xml
RUN mvn dependency:copy-dependencies -DoutputDirectory=/deps -DincludeScope=runtime -q

FROM keycloak/keycloak:26.5

USER root

# Copy all resolved dependencies (correct, compatible versions) into providers
COPY --from=deps /deps/*.jar /opt/keycloak/providers/

# Fix ownership of downloaded JARs for keycloak user (UID 1000)
RUN chown -R 1000:1000 /opt/keycloak/providers

USER 1000

COPY keycloak/cpln-realm.json /opt/keycloak/data/import/adventra-realm.json
RUN /opt/keycloak/bin/kc.sh build \
    --db=postgres \
    --health-enabled=true \
    --metrics-enabled=true

CMD ["start", "--optimized", "--import-realm", "--http-enabled=true", "--proxy-headers=xforwarded", "--hostname-strict=false"]