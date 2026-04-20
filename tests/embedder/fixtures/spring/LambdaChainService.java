package com.example.demo.service;

import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.*;
import java.util.function.*;

@Service
public class LambdaChainService {

    public List<ReportLine> buildReport(List<Department> departments) {
        return departments.stream()
                .filter(d -> !d.getEmployees().isEmpty())
                .flatMap(d -> d.getEmployees().stream()
                        .filter(e -> e.getSalary() > 5000)
                        .map(e -> new ReportLine(
                                d.getName(),
                                e.getName(),
                                e.getSalary()
                        ))
                )
                .sorted(Comparator
                        .comparing(ReportLine::department)
                        .thenComparing(ReportLine::salary, Comparator.reverseOrder()))
                .collect(Collectors.toList());
    }

    public Map<String, DoubleSummaryStatistics> salaryStats(List<Employee> employees) {
        return employees.stream()
                .collect(Collectors.groupingBy(
                        Employee::getDepartment,
                        Collectors.summarizingDouble(Employee::getSalary)
                ));
    }

    public Optional<Employee> findTopEarner(List<Department> departments) {
        return departments.stream()
                .flatMap(d -> d.getEmployees().stream())
                .max(Comparator.comparingDouble(Employee::getSalary));
    }

    record ReportLine(String department, String employee, double salary) {}
}
