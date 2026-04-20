package com.example.service;
import org.springframework.stereotype.Service;
import java.util.*;
import java.util.stream.*;
import java.util.function.*;
@Service
public class StreamProcessingAdvanced {
    public Map<String, Long> countByCategory(List<String> items) {
        return items.stream()
            .collect(Collectors.groupingBy(s -> s.split(":")[0], Collectors.counting()));
    }
    public List<String> flatMapTransform(List<List<String>> nested) {
        return nested.stream()
            .flatMap(Collection::stream)
            .filter(Objects::nonNull)
            .map(String::trim)
            .distinct()
            .sorted()
            .collect(Collectors.toList());
    }
    public <T, R> List<R> transformBatch(List<T> items, Function<T, R> mapper, Predicate<R> filter) {
        return items.stream()
            .map(mapper)
            .filter(filter)
            .collect(Collectors.toList());
    }
    public Optional<String> findFirstMatching(List<String> items, String prefix) {
        return items.stream()
            .filter(s -> s.startsWith(prefix))
            .findFirst();
    }
    public IntSummaryStatistics computeStats(List<String> items) {
        return items.stream()
            .mapToInt(String::length)
            .summaryStatistics();
    }
}
