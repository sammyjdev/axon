package com.example.service;
import org.springframework.stereotype.Service;
import java.util.*;
@Service
public class NestedGenericService {
    public Map<String, List<Optional<String>>> buildIndex(List<String> items) {
        Map<String, List<Optional<String>>> index = new HashMap<>();
        for (String item : items) {
            String key = item.substring(0, 1);
            index.computeIfAbsent(key, k -> new ArrayList<>()).add(Optional.of(item));
        }
        return index;
    }
    public <K, V extends Comparable<V>> Map.Entry<K, V> findMaxEntry(Map<K, V> map) {
        return map.entrySet().stream()
            .max(Map.Entry.comparingByValue())
            .orElseThrow();
    }
    public Map<String, Map<String, List<Integer>>> buildDeepIndex(List<String> data) {
        Map<String, Map<String, List<Integer>>> result = new TreeMap<>();
        for (int i = 0; i < data.size(); i++) {
            String item = data.get(i);
            result.computeIfAbsent(item.substring(0, 1), k -> new HashMap<>())
                  .computeIfAbsent(item, k -> new ArrayList<>())
                  .add(i);
        }
        return result;
    }
}
