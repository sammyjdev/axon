package com.example.service;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;

@Service
public class WildcardService {
    public double sumNumbers(List<? extends Number> numbers) {
        return numbers.stream().mapToDouble(Number::doubleValue).sum();
    }

    public void addAll(List<? super Integer> dest, List<Integer> src) {
        dest.addAll(src);
    }

    public <T> void copy(List<? extends T> src, List<? super T> dest) {
        dest.addAll(src);
    }

    public Number findLargest(List<? extends Number> items) {
        return items.stream()
            .max(Comparator.comparingDouble(Number::doubleValue))
            .orElseThrow();
    }
}
