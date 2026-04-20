package com.example.demo.model;

import java.time.Instant;

public record AuditLog(
        Long id,
        String entityType,
        Long entityId,
        String action,
        String performedBy,
        String details,
        Instant occurredAt
) {
    public AuditLog {
        if (entityType == null || entityType.isBlank()) {
            throw new IllegalArgumentException("entityType required");
        }
        if (action == null || action.isBlank()) {
            throw new IllegalArgumentException("action required");
        }
        if (occurredAt == null) {
            occurredAt = Instant.now();
        }
    }

    public String summary() {
        return performedBy + " performed " + action + " on " + entityType + "#" + entityId;
    }

    public boolean isRecent(int withinSeconds) {
        return occurredAt.isAfter(Instant.now().minusSeconds(withinSeconds));
    }
}
