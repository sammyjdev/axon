package com.example.service;

import org.springframework.stereotype.Service;
import java.util.*;

@Service
public class GenericRepositoryService {
    public <T extends Identifiable> T saveWithAudit(T entity) {
        System.out.println("Saving: " + entity.getId());
        return entity;
    }

    public <T extends Comparable<T>> T findMax(List<T> items) {
        return items.stream().max(Comparable::compareTo).orElseThrow();
    }

    public <T extends Scoreable> T findWithHighestScore(List<T> items) {
        return items.stream().max(Comparator.comparingDouble(Scoreable::score)).orElseThrow();
    }

    public <T extends Identifiable> boolean deleteIfExists(T entity) {
        System.out.println("Delete attempt: " + entity.getId());
        return true;
    }
}
