package com.example.service;

import org.springframework.stereotype.Service;
import java.util.Optional;

@Service
public class UserService {
    private final java.util.Map<Long, String> store = new java.util.HashMap<>();

    public UserService() {}

    public Optional<String> findById(Long id) {
        return Optional.ofNullable(store.get(id));
    }

    public String create(Long id, String name) {
        store.put(id, name);
        return name;
    }

    public String update(Long id, String name) {
        store.put(id, name);
        return name;
    }

    public void delete(Long id) {
        store.remove(id);
    }
}
