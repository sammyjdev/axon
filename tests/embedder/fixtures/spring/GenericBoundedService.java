package com.example.service;
import org.springframework.stereotype.Service;
import java.io.Serializable;
import java.util.List;
import java.util.stream.Collectors;
@Service
public class GenericBoundedService<T extends Comparable<T> & Serializable> {
    private final List<T> items;
    public GenericBoundedService(List<T> items) { this.items = items; }
    public T findMin() { return items.stream().min(Comparable::compareTo).orElseThrow(); }
    public T findMax() { return items.stream().max(Comparable::compareTo).orElseThrow(); }
    public List<T> sortedItems() { return items.stream().sorted().collect(Collectors.toList()); }
    public List<T> filterGreaterThan(T threshold) {
        return items.stream().filter(i -> i.compareTo(threshold) > 0).collect(Collectors.toList());
    }
}
