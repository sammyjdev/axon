package com.example.demo.service;

import java.time.Instant;

public abstract class AbstractAuditService<T extends Auditable> {

    protected abstract T findById(Long id);

    protected abstract T save(T entity);

    public T touchAndSave(Long id) {
        T entity = findById(id);
        entity.setUpdatedAt(Instant.now());
        return save(entity);
    }

    public boolean exists(Long id) {
        try {
            findById(id);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    protected void validateNotNull(Object value, String fieldName) {
        if (value == null) {
            throw new IllegalArgumentException(fieldName + " must not be null");
        }
    }
}
