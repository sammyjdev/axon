# Spring Boot XML-to-Annotation Migration

> Goal: Migrate the legacy Spring XML bean configuration to annotation-based Java config without breaking runtime behavior.

## Context

The `payments-service` module still boots from `applicationContext.xml`. We want
idiomatic `@Configuration` classes and constructor injection instead of XML
property wiring.

## Tasks

### 1. Audit the existing XML configuration
Inventory every bean declared in `applicationContext.xml`, including scopes,
init/destroy methods, and property wiring. Produce a dependency table.

### 2. Introduce Java @Configuration classes
- depends_on: 1
Create one `@Configuration` class per logical bean group, mirroring the audited
XML beans. Do not delete the XML yet.

### 3. Migrate bean definitions to @Bean methods
- depends_on: 2
Move each bean definition into an `@Bean` method, replacing property wiring with
constructor injection.

### 4. Switch the application bootstrap
- depends_on: 3
Replace `ClassPathXmlApplicationContext` with `AnnotationConfigApplicationContext`
and load the new configuration classes.

### 5. Remove XML and verify
- depends_on: 4
Delete `applicationContext.xml`, run the full regression suite, and confirm the
service boots cleanly with no XML on the classpath.

## Acceptance

All integration tests pass and `applicationContext.xml` no longer exists.
