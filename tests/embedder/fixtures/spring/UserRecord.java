package com.example.model;

public record UserRecord(Long id, String name, String email) {
    public UserRecord {
        if (name == null || name.isBlank()) throw new IllegalArgumentException("name required");
        if (email == null || !email.contains("@")) throw new IllegalArgumentException("invalid email");
    }
    public String displayName() { return name + " <" + email + ">"; }
}
