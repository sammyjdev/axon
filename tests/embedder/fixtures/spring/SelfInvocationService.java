package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;

@Service
public class SelfInvocationService {
    private final Map<Long, Object> store = new HashMap<>();

    public SelfInvocationService() {}

    @Transactional
    public List<Object> processAll(List<Long> ids) {
        List<Object> results = new ArrayList<>();
        for (Long id : ids) {
            results.add(processSingle(id));
        }
        return results;
    }

    @Transactional(propagation = org.springframework.transaction.annotation.Propagation.REQUIRES_NEW)
    public Object processSingle(Long id) {
        return store.getOrDefault(id, "default-" + id);
    }

    public Object getItem(Long id) {
        return store.get(id);
    }
}
