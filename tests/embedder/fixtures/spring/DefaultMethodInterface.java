package com.example.interfaces;
import java.util.List;
import java.util.Optional;
public interface DefaultMethodInterface<T> {
    List<T> findAll();
    Optional<T> findById(Long id);
    T save(T entity);
    default boolean exists(Long id) { return findById(id).isPresent(); }
    default T findOrThrow(Long id) {
        return findById(id).orElseThrow(() -> new RuntimeException("Not found: " + id));
    }
    default List<T> saveAll(List<T> entities) {
        return entities.stream().map(this::save).toList();
    }
    default void deleteIfExists(Long id) {
        if (exists(id)) { System.out.println("Deleting " + id); }
    }
}
