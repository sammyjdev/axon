package com.example.demo.service;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TransactionalService {

    private final AccountRepository accountRepository;

    public TransactionalService(AccountRepository accountRepository) {
        this.accountRepository = accountRepository;
    }

    @Transactional
    public void transfer(Long fromId, Long toId, double amount) {
        Account from = accountRepository.findById(fromId)
                .orElseThrow(() -> new RuntimeException("Account not found: " + fromId));
        Account to = accountRepository.findById(toId)
                .orElseThrow(() -> new RuntimeException("Account not found: " + toId));
        from.debit(amount);
        to.credit(amount);
        accountRepository.save(from);
        accountRepository.save(to);
    }

    @Transactional(readOnly = true)
    public double getBalance(Long accountId) {
        return accountRepository.findById(accountId)
                .map(Account::getBalance)
                .orElse(0.0);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void auditTransfer(Long fromId, Long toId, double amount) {
        accountRepository.saveAudit(fromId, toId, amount);
    }

    @Transactional(isolation = Isolation.SERIALIZABLE)
    public void reserveFunds(Long accountId, double amount) {
        Account account = accountRepository.findByIdForUpdate(accountId)
                .orElseThrow(() -> new RuntimeException("Account not found: " + accountId));
        if (account.getBalance() < amount) {
            throw new InsufficientFundsException(accountId, amount);
        }
        account.reserve(amount);
        accountRepository.save(account);
    }
}
