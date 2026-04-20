package com.example.service;

import org.springframework.stereotype.Service;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class StreamProcessingService {
    public Map<String, Long> groupAndCountByCategory(List<String> items) {
        return items.stream()
            .collect(Collectors.groupingBy(s -> s.split(":")[0], Collectors.counting()));
    }

    public List<String> filterAndSort(List<String> items, String prefix) {
        return items.stream()
            .filter(s -> s.startsWith(prefix))
            .sorted()
            .collect(Collectors.toList());
    }

    public OptionalDouble averageLength(List<String> items) {
        return items.stream().mapToInt(String::length).average();
    }

    public Map<Integer, List<String>> partitionByLength(List<String> items) {
        return items.stream().collect(Collectors.groupingBy(String::length));
    }

    public List<String> flattenAndDistinct(List<List<String>> nested) {
        return nested.stream()
            .flatMap(Collection::stream)
            .distinct()
            .sorted()
            .collect(Collectors.toList());
    }
}
